"""本地审计脱敏病例中的医院、医生角色与病种覆盖。

脚本只读取压缩包内的图片，显式跳过 ``_manifest.json`` 和 ``__MACOSX``；
OCR 全程本地执行，不调用 DeepSeek。所有含 OCR 原文或真实名称的内容仅写入
被 git 忽略的 ``work/provider_audit/private``。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import secrets
import sys
import tempfile
from zipfile import ZipFile

from rapidocr import RapidOCR

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medical_claims.provider_extraction import (  # noqa: E402
    build_safe_context,
    extract_institution_candidates,
)
from medical_claims.providers import OcrBlock, OcrDocument  # noqa: E402


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=ROOT / "基于理赔医护记录的“医护洞察与温度指引”引擎.zip",
        help="包含 CEO命题/output_dir.zip 的外层压缩包",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "work" / "provider_audit")
    parser.add_argument("--max-cases", type=int, default=None, help="仅用于冒烟验证")
    parser.add_argument("--max-images-per-case", type=int, default=None, help="仅用于冒烟验证")
    return parser.parse_args()


def json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_or_create_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    secret = secrets.token_bytes(32)
    path.write_text(secret.hex(), encoding="ascii")
    return secret


def locate_inner_zip(outer: ZipFile) -> str:
    candidates = [name for name in outer.namelist() if name.endswith("output_dir.zip")]
    if len(candidates) != 1:
        raise RuntimeError(f"应恰好找到一个 output_dir.zip，实际找到 {len(candidates)} 个")
    return candidates[0]


def image_case_id(name: str) -> str | None:
    parts = PurePosixPath(name).parts
    if "__MACOSX" in parts or len(parts) < 3:
        return None
    for part in parts:
        if part.startswith("CL") and len(part) >= 4:
            return part
    return None


def cached_ocr(
    engine: RapidOCR,
    image_bytes: bytes,
    document_id: str,
    cache_dir: Path,
) -> OcrDocument:
    digest = hashlib.sha256(image_bytes).hexdigest()
    cache_path = cache_dir / f"{digest}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        cached = OcrDocument.model_validate(payload)
        return cached.model_copy(update={"document_id": document_id})

    result = engine(image_bytes)
    txts = list(result.txts) if result.txts is not None else []
    scores = list(result.scores) if result.scores is not None else []
    boxes = list(result.boxes) if result.boxes is not None else []
    blocks: list[OcrBlock] = []
    for index, text in enumerate(txts):
        score = float(scores[index]) if index < len(scores) else None
        bbox = None
        if index < len(boxes):
            points = boxes[index]
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            bbox = (min(xs), min(ys), max(xs), max(ys))
        blocks.append(OcrBlock(text=str(text), page=1, confidence=score, bbox=bbox))
    document = OcrDocument(document_id=document_id, blocks=blocks)
    json_dump(cache_path, document.model_dump(mode="json"))
    return document


def build_markdown(report: dict) -> str:
    lines = [
        "# 第四问机构数据覆盖审计",
        "",
        f"- 审计案件：{report['scope']['case_count']} 个",
        f"- OCR 图片：{report['scope']['image_count']} 张",
        f"- 识别出机构的案件：{report['institution_coverage']['identified_cases']} 个（{report['institution_coverage']['rate']:.1%}）",
        f"- 脱敏机构数：{report['institution_coverage']['unique_institutions']} 家",
        f"- 识别出带角色医生的案件：{report['clinician_coverage']['cases_with_clinicians']} 个",
        "",
        "## 3 个目标病种 × 医院覆盖",
        "",
        "| 病种 | 病例数 | 覆盖机构数 | 是否达到至少 5 家 |",
        "|---|---:|---:|:---:|",
    ]
    labels = {
        "pneumonia": "肺炎",
        "fracture": "骨折",
        "diabetes": "糖尿病",
        "hypertension": "高血压",
        "coronary_heart_disease": "冠心病",
        "cerebral_infarction": "脑梗死",
        "lung_nodule": "肺结节",
        "influenza": "流感",
        "urinary_calculus": "泌尿系结石",
        "appendicitis": "阑尾炎",
        "cholecystitis": "胆囊炎",
        "copd": "慢阻肺",
        "asthma": "哮喘",
        "lumbar_disc_herniation": "腰椎间盘突出",
        "cataract": "白内障",
    }
    for disease in report["disease_institution_coverage"]:
        item = report["disease_institution_coverage"][disease]
        lines.append(
            f"| {labels.get(disease, disease)} | {item['case_count']} | {item['institution_count']} | {'是' if item['meets_five_institutions'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 数据边界",
            "",
            "- 案件文件夹编号只作为 case_id，不能当作稳定患者编号，因此当前不能据此计算再入院率。",
            "- 报告医师、检验者、审核者等仅作为文档责任角色，不能自动计入经治医生画像。",
            "- 未读取 `_manifest.json`，未调用任何外部大模型。真实名称映射只保存在本机 private 目录。",
            "- 患者反馈字段不在现有病例图片中，当前应标记为不可用，而不是由模型编造。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    archive = args.archive.resolve()
    output = args.output.resolve()
    private_dir = output / "private"
    cache_dir = private_dir / "ocr_cache"
    secret = load_or_create_secret(private_dir / "hmac.key")

    if not archive.exists():
        raise FileNotFoundError(archive)

    print(f"[audit] 打开外层压缩包：{archive}", flush=True)
    with ZipFile(archive) as outer:
        inner_name = locate_inner_zip(outer)
        print(f"[audit] 找到内层数据包：{inner_name}", flush=True)
        with outer.open(inner_name) as source, tempfile.SpooledTemporaryFile(max_size=128 * 1024 * 1024) as spool:
            while chunk := source.read(8 * 1024 * 1024):
                spool.write(chunk)
            spool.seek(0)
            with ZipFile(spool) as inner:
                case_images: dict[str, list[str]] = defaultdict(list)
                for name in inner.namelist():
                    if PurePosixPath(name).suffix.lower() not in IMAGE_EXTENSIONS:
                        continue
                    case_id = image_case_id(name)
                    if case_id:
                        case_images[case_id].append(name)

                selected_cases = sorted(case_images)
                if args.max_cases is not None:
                    selected_cases = selected_cases[: args.max_cases]
                selected: dict[str, list[str]] = {}
                for case_id in selected_cases:
                    names = sorted(case_images[case_id])
                    if args.max_images_per_case is not None:
                        names = names[: args.max_images_per_case]
                    selected[case_id] = names

                total_images = sum(len(names) for names in selected.values())
                print(f"[audit] 将处理 {len(selected)} 个案件、{total_images} 张图片；已跳过 manifest 和 Mac 资源文件", flush=True)
                engine = RapidOCR()

                contexts = []
                private_mapping: dict[str, dict[str, str]] = {
                    "institutions": {},
                    "clinicians": {},
                    "documents": {},
                }
                review_queue: list[dict[str, object]] = []
                processed = 0
                empty_ocr_images = 0
                for case_index, (case_id, names) in enumerate(selected.items(), start=1):
                    documents: list[OcrDocument] = []
                    for page_index, name in enumerate(names, start=1):
                        image_bytes = inner.read(name)
                        document_id = "IMG-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12].upper()
                        private_mapping["documents"][document_id] = name
                        document = cached_ocr(engine, image_bytes, document_id, cache_dir)
                        documents.append(document)
                        if not document.blocks:
                            empty_ocr_images += 1
                        processed += 1
                        print(
                            f"[audit] OCR {processed}/{total_images} | 案件 {case_index}/{len(selected)} | {case_id} | 页面 {page_index}/{len(names)}",
                            flush=True,
                        )
                    context, mapping = build_safe_context(case_id, documents, secret)
                    contexts.append(context)
                    private_mapping["institutions"].update(mapping["institutions"])
                    private_mapping["clinicians"].update(mapping["clinicians"])
                    if context.institution is not None and context.institution.review_required:
                        grouped_candidates: dict[str, list] = defaultdict(list)
                        for candidate in extract_institution_candidates(documents):
                            grouped_candidates[candidate.value].append(candidate)
                        selected_name = mapping["institutions"].get(
                            context.institution.institution_id, ""
                        )
                        review_queue.append(
                            {
                                "case_id": case_id,
                                "selected_institution_id": context.institution.institution_id,
                                "selected_institution_name": selected_name,
                                "review_status": "pending",
                                "reviewer": None,
                                "reviewed_at": None,
                                "decision": None,
                                "institution_candidates": [
                                    {
                                        "name": name,
                                        "occurrences": len(candidates),
                                        "mean_ocr_confidence": round(
                                            sum(item.confidence for item in candidates)
                                            / len(candidates),
                                            4,
                                        ),
                                        "sources": sorted(
                                            {
                                                f"{item.document_id}:page-{item.page}"
                                                for item in candidates
                                            }
                                        ),
                                    }
                                    for name, candidates in sorted(
                                        grouped_candidates.items(),
                                        key=lambda item: (
                                            -len(item[1]),
                                            -sum(candidate.confidence for candidate in item[1]),
                                            item[0],
                                        ),
                                    )
                                ],
                            }
                        )

    safe_payload = [context.model_dump(mode="json") for context in contexts]
    json_dump(output / "provider-contexts.safe.json", safe_payload)
    json_dump(private_dir / "provider-mapping.private.json", private_mapping)
    json_dump(private_dir / "provider-review-queue.private.json", review_queue)

    institution_ids = [
        context.institution.institution_id
        for context in contexts
        if context.institution is not None
    ]
    roles = Counter(
        clinician.role.value
        for context in contexts
        for clinician in context.clinicians
    )
    disease_names = sorted({disease for context in contexts for disease in context.disease_groups})
    disease_matrix: dict[str, Counter[str]] = {disease: Counter() for disease in disease_names}
    disease_cases = Counter()
    for context in contexts:
        for disease in context.disease_groups:
            if disease not in disease_matrix:
                continue
            disease_cases[disease] += 1
            if context.institution is not None:
                disease_matrix[disease][context.institution.institution_id] += 1

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {
            "case_count": len(contexts),
            "image_count": sum(len(names) for names in selected.values()),
            "partial_run": args.max_cases is not None or args.max_images_per_case is not None,
            "manifest_used": False,
            "external_llm_used": False,
            "unique_image_contents": len(list(cache_dir.glob("*.json"))),
            "empty_ocr_images": empty_ocr_images,
        },
        "institution_coverage": {
            "identified_cases": len(institution_ids),
            "missing_cases": len(contexts) - len(institution_ids),
            "rate": round(len(institution_ids) / len(contexts), 4) if contexts else 0,
            "unique_institutions": len(set(institution_ids)),
            "review_required_cases": sum(
                context.institution is not None and context.institution.review_required
                for context in contexts
            ),
        },
        "clinician_coverage": {
            "cases_with_clinicians": sum(bool(context.clinicians) for context in contexts),
            "unique_clinicians": len({c.clinician_id for context in contexts for c in context.clinicians}),
            "ranking_eligible_clinicians": len(
                {c.clinician_id for context in contexts for c in context.clinicians if c.ranking_eligible}
            ),
            "role_counts": dict(sorted(roles.items())),
        },
        "disease_institution_coverage": {},
        "limitations": [
            "案件编号不是稳定患者编号，不能据此计算再入院率",
            "患者反馈在当前病例图片中不可用",
            "医生必须带明确角色；报告、检验、审核角色不进入经治医生排名",
            "OCR 候选仍需人工抽样复核",
        ],
    }
    for disease, matrix in disease_matrix.items():
        report["disease_institution_coverage"][disease] = {
            "case_count": disease_cases[disease],
            "institution_count": len(matrix),
            "meets_five_institutions": len(matrix) >= 5,
            "institution_case_counts": dict(sorted(matrix.items())),
        }
    report["requirement_check"] = {
        "diseases_meeting_five_institutions": sorted(
            disease
            for disease, item in report["disease_institution_coverage"].items()
            if item["meets_five_institutions"]
        ),
    }
    report["requirement_check"]["meets_three_diseases_by_five_institutions"] = (
        len(report["requirement_check"]["diseases_meeting_five_institutions"]) >= 3
    )

    json_dump(output / "provider-coverage-report.json", report)
    (output / "provider-coverage-report.md").write_text(build_markdown(report), encoding="utf-8")
    print(f"[audit] 完成。安全报告：{output / 'provider-coverage-report.md'}", flush=True)
    print(f"[audit] 安全上下文：{output / 'provider-contexts.safe.json'}", flush=True)
    print(f"[audit] 私有映射和 OCR 缓存仅保存在：{private_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
