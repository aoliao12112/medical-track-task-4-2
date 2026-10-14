"""Generate a read-only historical evidence revalidation report."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medical_claims.evidence_revalidation import build_revalidation_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "outputs" / "competition.db",
        help="SQLite database opened with mode=ro and query_only enabled",
    )
    parser.add_argument("--case-id", action="append", default=[], help="Only audit this case; repeatable")
    parser.add_argument("--output", type=Path, help="Report JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or ROOT / "outputs" / "logs" / f"evidence-revalidation-{stamp}.json"
    report = build_revalidation_report(args.database, case_ids=args.case_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report_path={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
