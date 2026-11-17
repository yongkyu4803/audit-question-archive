#!/usr/bin/env python3
"""처리기록(_state/처리기록.json) 관리: Drive 폴더 id 등록, 새 원본 찾기, 현황 보기.

  ledger.py set-folders --origin <링크|id> --parse <링크|id> --card <링크|id> --root R
  ledger.py diff --listing 목록.json --root R     # Drive 원본/ 목록과 비교해 새 파일·바뀐 파일을 찾는다
  ledger.py status --root R                        # 원본별 진행 상태

목록.json 은 Drive 원본/ 폴더를 search_files 로 조회한 결과(파일 배열 또는 {"files":[...]}) 를 그대로 저장한 것이다.
각 항목에서 id, title, modifiedTime, etag, fileSize 를 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from archive_lib import (ORIGINAL_EXTS, drive_id_from, iter_cards, load_ledger, publishable, resolve_root,
                         save_ledger)

FOLDER_MIME = "application/vnd.google-apps.folder"


def load_listing(path: str) -> list[dict]:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("files", [])
    return data


def cmd_set_folders(a, root):
    led = load_ledger(root)
    for key, val in (("원본", a.origin), ("파싱", a.parse), ("카드", a.card)):
        if val:
            led["folders"][key] = drive_id_from(val)
    save_ledger(root, led)
    print("Drive 폴더:", json.dumps(led["folders"], ensure_ascii=False))


def cmd_diff(a, root):
    led = load_ledger(root)
    seen = led["originals"]
    new, changed, ignored, same = [], [], [], []
    listed_ids = set()
    for f in load_listing(a.listing):
        fid, name = f.get("id", ""), f.get("title") or f.get("name", "")
        if f.get("trashed"):
            continue
        listed_ids.add(fid)
        ext = Path(name).suffix.lower()
        if f.get("mimeType") == FOLDER_MIME or name.startswith("_") or ext not in ORIGINAL_EXTS:
            ignored.append(name)
            continue
        rec = {"id": fid, "name": name, "modifiedTime": f.get("modifiedTime", ""), "etag": f.get("etag", ""),
               "size": f.get("fileSize", "")}
        old = seen.get(fid)
        if old is None:
            new.append(rec)
        elif (old.get("etag") and rec["etag"] and old["etag"] != rec["etag"]) or \
             (old.get("modified") and rec["modifiedTime"] and old["modified"] != rec["modifiedTime"]):
            changed.append({**rec, "card_id": old.get("card_id")})
        else:
            same.append(name)
    missing = [{"id": k, "name": v.get("name", ""), "card_id": v.get("card_id")}
               for k, v in seen.items() if k not in listed_ids]
    out = {"새 원본": new, "바뀐 원본(의심)": changed, "Drive 에서 사라진 원본": missing,
           "무시한 항목": ignored, "이미 처리됨": len(same)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if new or changed or missing:
        print(f"\n새 원본 {len(new)}건, 바뀐 원본 {len(changed)}건, 사라진 원본 {len(missing)}건", file=sys.stderr)
    else:
        print("\n새로 처리할 원본이 없습니다.", file=sys.stderr)


def cmd_status(a, root):
    led = load_ledger(root)
    cards = {fm["id"]: fm for _, fm in iter_cards(root) if fm and fm.get("id")}
    rows = []
    for did, o in led["originals"].items():
        cid = o.get("card_id")
        if not cid or cid not in cards:
            state = "카드 없음"
        else:
            fm = cards[cid]
            if fm.get("visibility") == "비공개":
                state = "비공개(올리지 않음)"
            elif not publishable(fm):
                state = "개인정보 확인 대기"
            elif f"card:{cid}" in led["uploads"] and f"parse:{cid}" in led["uploads"]:
                state = "게시됨"
            else:
                state = "업로드 대기"
        rows.append((state, o.get("name", ""), cid or "-"))
    if not rows:
        print("처리한 원본이 없습니다.")
        return
    for state, name, cid in sorted(rows):
        print(f"[{state}] {name}  →  {cid}")
    print("\nDrive 폴더:", json.dumps(led["folders"], ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set-folders")
    s.add_argument("--origin"); s.add_argument("--parse"); s.add_argument("--card")
    d = sub.add_parser("diff")
    d.add_argument("--listing", required=True)
    sub.add_parser("status")
    # 하위 명령 뒤에 --root 를 써도 되게 한다
    for sp in (s, d, sub.choices["status"]):
        sp.add_argument("--root", dest="root_sub")
    a = ap.parse_args()
    root = resolve_root(a.root_sub or a.root)
    {"set-folders": cmd_set_folders, "diff": cmd_diff, "status": cmd_status}[a.cmd](a, root)


if __name__ == "__main__":
    main()
