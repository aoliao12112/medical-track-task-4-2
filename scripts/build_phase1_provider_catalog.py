"""从已核验的公开医院审计生成第四问 Phase 1 独立候选目录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from medical_claims.disease_provider_catalog import build_catalog_from_public_audit, write_catalog


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "outputs" / "hospital_public_data_audit.json",
        help="已核验公开医院审计 JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "knowledge" / "quality" / "disease_provider_catalog.json",
        help="去除病例标识后的 Phase 1 目录",
    )
    args = parser.parse_args()
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    catalog = build_catalog_from_public_audit(payload)
    write_catalog(catalog, args.output)
    counts = {item.disease_group: len(item.hospitals) for item in catalog.diseases}
    print(json.dumps({"output": str(args.output), "counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
