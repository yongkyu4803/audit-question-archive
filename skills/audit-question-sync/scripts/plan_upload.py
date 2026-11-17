#!/usr/bin/env python3
"""Drive 에 올리거나 바꾸거나 내릴 파일 목록(JSON)을 만든다. 이 스크립트는 Drive 를 직접 만지지 않는다.

Claude 가 목록의 작업을 Drive 도구로 수행하고, 하나 끝날 때마다 record_upload.py 로 결과를 기록한다.

작업 종류
  create   Drive 에 없는 파일: create_file(parentId=folder_id, title, base64Content=file_ref, text/markdown)
  replace  이미 올라간 파일의 내용이 바뀜: 새 파일을 create_file 한 뒤 record_upload.py 로 기록하고, old_drive_id 를 trash_file
           (update_file 은 내용을 못 바꾼다. 새로 올린 뒤에 옛 것을 내려서, 그 사이에 파일이 비는 일이 없게 한다)
  remove   올라가 있으면 안 되는 파일(비공개로 바뀜, 개인정보 확인 취소 등): trash_file(drive_id)

올릴 대상은 공개·내부이면서 개인정보 확인이 끝난 카드와 그 파싱본, 카드/_INDEX.md 이다.
--ledger-only 는 모든 기록이 끝난 뒤 처리기록 사본(원본/ 폴더, 관리자 전용)을 올릴 작업만 만든다.

  python3 plan_upload.py --root R
  python3 plan_upload.py --root R --ledger-only
"""
from __future__ import annotations

import argparse
import json
import sys

from archive_lib import (CARD_SUFFIX, DIR_CARD, DIR_STATE, PARSE_SUFFIX, iter_cards, ledger_snapshot_text, load_ledger,
                         parse_path, publishable, resolve_root, sha256_file, sha256_text)
from rebuild_index import rebuild


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    ap.add_argument("--ledger-only", action="store_true")
    a = ap.parse_args()
    root = resolve_root(a.root)
    led = load_ledger(root)
    folders = led.get("folders", {})
    need = ["원본", "파싱", "카드"]
    if [k for k in need if k not in folders]:
        print("오류: Drive 폴더 id 가 없습니다. 먼저 ledger.py set-folders 로 원본/파싱/카드 폴더를 등록하세요.", file=sys.stderr)
        sys.exit(2)
    uploads = led["uploads"]
    tasks = []

    def ref(path):
        return f"sha256:{sha256_file(path)}:{path}"

    if a.ledger_only:
        snap = ledger_snapshot_text(led)
        sp = root / DIR_STATE / "처리기록.upload.json"
        sp.write_text(snap, encoding="utf-8")
        sha = sha256_text(snap)
        up = uploads.get("ledger")
        if up is None or up.get("sha256") != sha:
            tasks.append({"action": "create" if up is None else "replace", "kind": "ledger", "id": None,
                          "local_path": str(sp), "file_ref": ref(sp), "title": "_처리기록.json", "folder": "원본",
                          "folder_id": folders["원본"], "content_type": "application/json", "sha256": sha,
                          "old_drive_id": up.get("drive_id") if up else None})
        print(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2))
        print(f"\n작업 {len(tasks)}건", file=sys.stderr)
        return

    rebuild(root)  # 목록을 최신 카드로
    cards = {}
    for p, fm in iter_cards(root):
        if fm and fm.get("id"):
            cards[fm["id"]] = (p, fm)

    for cid, (cpath, fm) in cards.items():
        pub = publishable(fm)
        for kind, path, folder, title in (
            ("parse", parse_path(root, cid), "파싱", f"{cid}{PARSE_SUFFIX}"),
            ("card", cpath, "카드", f"{cid}{CARD_SUFFIX}"),
        ):
            key, up = f"{kind}:{cid}", uploads.get(f"{kind}:{cid}")
            if pub:
                if not path.exists():
                    print(f"경고: {path} 가 없어 건너뜁니다", file=sys.stderr)
                    continue
                sha = sha256_file(path)
                if up is None or up.get("sha256") != sha:
                    tasks.append({"action": "create" if up is None else "replace", "kind": kind, "id": cid,
                                  "local_path": str(path), "file_ref": ref(path), "title": title, "folder": folder,
                                  "folder_id": folders[folder], "content_type": "text/markdown", "sha256": sha,
                                  "old_drive_id": up.get("drive_id") if up else None})
            elif up is not None:
                tasks.append({"action": "remove", "kind": kind, "id": cid, "title": title, "folder": folder,
                              "drive_id": up.get("drive_id"), "reason": "비공개이거나 개인정보 확인이 취소됨"})

    for key, up in uploads.items():  # 로컬 카드가 사라졌는데 올라가 있는 것
        if key.startswith(("parse:", "card:")) and key.split(":", 1)[1] not in cards:
            kind, cid = key.split(":", 1)
            tasks.append({"action": "remove", "kind": kind, "id": cid, "title": up.get("title", ""),
                          "folder": "파싱" if kind == "parse" else "카드", "drive_id": up.get("drive_id"),
                          "reason": "작업본에 카드가 없음"})

    idx = root / DIR_CARD / "_INDEX.md"
    sha = sha256_file(idx)
    up = uploads.get("index")
    if (up is None and any(publishable(fm) for _, fm in cards.values())) or (up is not None and up.get("sha256") != sha):
        tasks.append({"action": "create" if up is None else "replace", "kind": "index", "id": None,
                      "local_path": str(idx), "file_ref": ref(idx), "title": "_INDEX.md", "folder": "카드",
                      "folder_id": folders["카드"], "content_type": "text/markdown", "sha256": sha,
                      "old_drive_id": up.get("drive_id") if up else None})

    order = {"create": 0, "replace": 0, "remove": 1}
    kind_order = {"parse": 0, "card": 1, "index": 2}
    tasks.sort(key=lambda t: (order[t["action"]], kind_order.get(t["kind"], 9)))
    print(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2))
    n = {k: sum(1 for t in tasks if t["action"] == k) for k in ("create", "replace", "remove")}
    print(f"\n올릴 것 {n['create']}건, 교체 {n['replace']}건, 내릴 것 {n['remove']}건", file=sys.stderr)
    if not tasks:
        print("Drive 와 작업본이 같습니다. 할 일이 없습니다.", file=sys.stderr)


if __name__ == "__main__":
    main()
