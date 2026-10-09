"""Extract one case from the nested official competition archive.

The outer archive contains ``CEO命题/output_dir.zip``.  This helper opens the
inner archive as a seekable stream, so it does not duplicate the 1 GB archive
on disk.  Existing files are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def readable_name(name: str) -> str:
    """Repair UTF-8 names that were stored without the ZIP UTF-8 flag."""

    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not args.case_id.startswith("CL") or not args.case_id[2:].isdigit():
        raise SystemExit("case_id 格式不正确")

    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise SystemExit(f"目标目录不是空目录，拒绝覆盖：{destination}")

    manifest: list[dict[str, object]] = []
    with ZipFile(args.archive) as outer:
        inner_names = [name for name in outer.namelist() if name.endswith("output_dir.zip")]
        if len(inner_names) != 1:
            raise SystemExit(f"应找到一个 output_dir.zip，实际找到 {len(inner_names)} 个")
        with outer.open(inner_names[0]) as inner_stream, ZipFile(inner_stream) as inner:
            prefix = f"output_dir/{args.case_id}/"
            entries = [entry for entry in inner.infolist() if entry.filename.startswith(prefix) and not entry.is_dir()]
            if not entries:
                raise SystemExit(f"压缩包中没有案件：{args.case_id}")
            for index, entry in enumerate(entries, start=1):
                original = PurePosixPath(entry.filename).name
                repaired = readable_name(original)
                safe_name = f"{index:02d}-{repaired}".replace("/", "_").replace("\\", "_")
                target = destination / safe_name
                with inner.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                manifest.append(
                    {
                        "page": index,
                        "source_entry": entry.filename,
                        "prepared_filename": safe_name,
                        "bytes": entry.file_size,
                    }
                )

    (destination.parent / "manifest.json").write_text(
        json.dumps(
            {
                "case_id": args.case_id,
                "source": args.archive.name,
                "document_count": len(manifest),
                "documents": manifest,
                "usage": "仅用于本地比赛原型验证；上传前仍需人工确认脱敏状态。",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"prepared {args.case_id}: {len(manifest)} documents -> {destination}")


if __name__ == "__main__":
    main()
