"""对长病例执行本地OCR并输出分块计划，不调用外部LLM。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from medical_claims.long_case import chunk_documents, document_text_size
from web.backend.app import recognize_image


SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def main() -> None:
    parser = argparse.ArgumentParser(description="长病例OCR与分块计划检查")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument("--max-characters", type=int, default=18_000)
    parser.add_argument("--max-ocr-blocks", type=int, default=600)
    args = parser.parse_args()

    files = sorted(
        [path for path in args.input_dir.iterdir() if path.suffix.lower() in SUPPORTED_SUFFIXES],
        key=natural_key,
    )
    if not files:
        raise SystemExit("输入目录没有支持的图片")

    documents = []
    for page, path in enumerate(files, start=1):
        print(f"OCR {page}/{len(files)} {path.name}", flush=True)
        documents.append(recognize_image(path, path.name, page))

    chunks = chunk_documents(
        documents,
        max_pages=args.max_pages,
        max_characters=args.max_characters,
        max_ocr_blocks=args.max_ocr_blocks,
    )
    payload = {
        "page_count": len(documents),
        "ocr_character_count": sum(document_text_size(document) for document in documents),
        "empty_pages": [document["page"] for document in documents if not document["full_text"].strip()],
        "chunks": [
            {
                "index": index,
                "pages": [document["page"] for document in chunk],
                "document_ids": [document["document_id"] for document in chunk],
                "characters": sum(document_text_size(document) for document in chunk),
                "ocr_blocks": sum(len(document.get("blocks") or []) for document in chunk),
            }
            for index, chunk in enumerate(chunks, start=1)
        ],
        "documents": documents,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"DONE pages={payload['page_count']} chars={payload['ocr_character_count']} "
        f"chunks={len(chunks)} empty_pages={payload['empty_pages']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
