"""Read persisted KR4 portfolio; never infer PASS from code or synthetic fixtures."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from medical_claims.storage import CaseStorage
from medical_claims.quality_coverage import audit_quality_coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/kr4-coverage.json")
    args = parser.parse_args()
    storage = CaseStorage(args.database)
    result = audit_quality_coverage(storage.load_portfolio(), storage.profile_dataset_summary())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for disease in result["diseases"]:
        print(f"{disease['disease_group']}: institutions {disease['institution_count']}/5, "
              f"qualified {disease['qualified_institution_count']}/5, "
              f"valid dimensions {disease['available_dimension_count']}/5 — {disease['status']}")
    print(f"KR4: {result['status']}; formal PASS: False; report: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
