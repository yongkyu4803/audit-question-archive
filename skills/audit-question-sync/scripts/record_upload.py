#!/usr/bin/env python3
"""Drive 작업 결과를 처리기록에 적는다. plan_upload.py 의 작업 하나를 끝낼 때마다 실행한다.

  record_upload.py --kind card  --id ID --drive-id NEWID --sha256 HASH --title ID.카드.md   # create / replace 후
  record_upload.py --kind index --drive-id NEWID --sha256 HASH --title _INDEX.md
  record_upload.py --kind ledger --drive-id NEWID --sha256 HASH --title _처리기록.json
  record_upload.py --kind card  --id ID --removed                                           # remove 후

replace 는 새 파일을 올려 기록한 다음, 옛 파일을 trash_file 로 내린다 (순서를 지킨다).
"""
from __future__ import annotations

import argparse
import sys

from archive_lib import load_ledger, now_utc, resolve_root, save_ledger


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    ap.add_argument("--kind", required=True, choices=["parse", "card", "index", "ledger"])
    ap.add_argument("--id")
    ap.add_argument("--drive-id")
    ap.add_argument("--sha256")
    ap.add_argument("--title", default="")
    ap.add_argument("--removed", action="store_true")
    a = ap.parse_args()
    if a.kind in ("parse", "card") and not a.id:
        print("오류: parse/card 는 --id 가 필요합니다.", file=sys.stderr)
        sys.exit(1)
    key = f"{a.kind}:{a.id}" if a.kind in ("parse", "card") else a.kind
    root = resolve_root(a.root)
    led = load_ledger(root)
    if a.removed:
        led["uploads"].pop(key, None)
        save_ledger(root, led)
        print(f"기록 삭제: {key}")
        return
    if not a.drive_id or not a.sha256:
        print("오류: --drive-id 와 --sha256 이 필요합니다.", file=sys.stderr)
        sys.exit(1)
    led["uploads"][key] = {"drive_id": a.drive_id, "sha256": a.sha256, "title": a.title, "uploaded_utc": now_utc()}
    save_ledger(root, led)
    print(f"기록: {key} → {a.drive_id}")


if __name__ == "__main__":
    main()
