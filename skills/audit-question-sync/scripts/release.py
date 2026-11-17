#!/usr/bin/env python3
"""카드의 공개 구분·개인정보 확인·주제를 바꾸고 목록을 다시 만든다. Drive 에 올릴 수 있는 상태인지 알려 준다.

`--privacy-checked true` 는 사람(담당자)이 개인정보·비공개 자료를 확인한 뒤에만 쓴다.
올리는 일은 plan_upload.py 와 Drive 도구가 한다. 이 스크립트는 로컬 카드만 고친다.

  python3 release.py <id> --privacy-checked true
  python3 release.py <id> --visibility 비공개
  python3 release.py <id> --topic "가상사업 자료 요구"
"""
from __future__ import annotations

import argparse
import sys

from archive_lib import VISIBILITIES, card_path, load_ledger, parse_front, publishable, resolve_root, set_field
from rebuild_index import rebuild


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id")
    ap.add_argument("--root")
    ap.add_argument("--visibility", choices=VISIBILITIES)
    ap.add_argument("--privacy-checked", choices=["true", "false"])
    ap.add_argument("--topic")
    ap.add_argument("--reply-status", choices=["미수령", "수령", "부분수령", "해당없음"])
    ap.add_argument("--reply-date")
    a = ap.parse_args()
    if all(v is None for v in (a.visibility, a.privacy_checked, a.topic, a.reply_status, a.reply_date)):
        print("오류: 바꿀 값을 하나 이상 주세요.", file=sys.stderr)
        sys.exit(1)
    if a.topic is not None and ("|" in a.topic or "\n" in a.topic):
        print("오류: 주제에는 '|' 와 줄바꿈을 쓸 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    root = resolve_root(a.root)
    cf = card_path(root, a.id)
    if not cf.is_file():
        print(f"오류: 카드를 찾지 못했습니다: {cf}", file=sys.stderr)
        sys.exit(1)
    text = cf.read_text(encoding="utf-8")
    for key, val in (("visibility", a.visibility), ("privacy_checked", a.privacy_checked), ("topic", a.topic),
                     ("reply_status", a.reply_status), ("reply_date", a.reply_date)):
        if val is not None:
            text = set_field(text, key, val)
    cf.write_text(text, encoding="utf-8")
    fm, _ = parse_front(text)
    n = rebuild(root)

    led = load_ledger(root)
    uploaded = f"card:{a.id}" in led["uploads"]
    if publishable(fm):
        state = "게시 대상 (plan_upload.py 로 올릴 목록을 만드세요)" if not uploaded else "게시 대상 (이미 올라간 카드 — 바뀐 내용은 교체 업로드됩니다)"
    elif fm.get("visibility") == "비공개":
        state = "비공개 — Drive 에 올리지 않음" + (" (이미 올라가 있으면 plan_upload.py 가 내리라고 안내합니다)" if uploaded else "")
    else:
        state = "개인정보 확인 대기 — Drive 에 올리지 않음" + (" (이미 올라가 있으면 plan_upload.py 가 내리라고 안내합니다)" if uploaded else "")
    print(f"카드: {cf.name}")
    print(f"상태: {state}")
    print(f"목록: 게시 대상 {n}건")


if __name__ == "__main__":
    main()
